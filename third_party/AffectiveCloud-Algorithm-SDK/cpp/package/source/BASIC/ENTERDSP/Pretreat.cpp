#include "Pretreat.h"
#include "NumCpp.hpp"
#include "Basic.hpp"
namespace basic
{
    namespace dsp
    {
        bool eegLoadCheck(const vectord &inputData, DeviceInfo *deviceInfo)
        {
            int maxCount = 0, minCount = 0;
            for (size_t i = 0; i < inputData.size(); i++)
            {
                if (deviceInfo->eegMaxVal() - inputData[i] <= 10)
                    maxCount++;
                if (inputData[i] - deviceInfo->eegMinVal() <= 10)
                    minCount++;
            }
            if (maxCount > 2 || minCount > 2)
                return false;
            else
                return true;
        }

        bool peprLoadCheck(const std::vector<int>& prData) {
            if (*std::min_element(prData.begin(), prData.end()) < 1000) {
                return false;
            } else {
                return true;
            }
        }

        vectord singleOutlierRemove(const vectord &inputData)
        {
            if (inputData.size() < 2)
                return inputData;

            //一阶差分
            vectord diff;
            for (size_t i = 0; i < inputData.size() - 1; i++)
            {
                diff.push_back(inputData[1 + i] - inputData[i]);
            }
            //过滤非离群点的一阶差分值
            auto ncDiff = nc::NdArray(diff, false);
            auto absNcDiff = nc::abs(ncDiff);
            auto median = nc::median(absNcDiff).item();

            for (size_t i = 0; i < ncDiff.size(); i++)
            {
                if (absNcDiff[i] < 50 * median)
                {
                    ncDiff[i] = 0;
                }
            }
            //端点扩展
            nc::NdArray<double> start = {-ncDiff.front()};
            nc::NdArray<double> end = {-ncDiff.back()};
            auto diffEx = nc::hstack({start, ncDiff, end});

            //一阶卷积，过滤得到一阶差分中连续两个不为零的值，即离群点
            nc::NdArray<double> first(diffEx.begin() + 1, diffEx.end());
            nc::NdArray<double> second(diffEx.begin(), diffEx.end() - 1);
            auto conv = nc::multiply(first, second);
            //一阶卷积归一化，用于标记离群点坐标
            for (auto &e : conv)
            {
                if (std::abs(e) > 0)
                    e = 1;
            }
            auto bias = nc::multiply(first, conv);
            //离群点偏差
            for (size_t i = 0; i < bias.size(); i++)
            {
                bias[i] += inputData[i];
            }
            return bias.toStlVector();
        }

        vectord doubleOutlierRemove(const vectord &inputData)
        {
            vectord outputData(inputData.size(), 0);
            vectord firstInput;
            vectord secondInput;
            for (size_t i = 0; i < inputData.size(); i += 2)
            {
                firstInput.push_back(inputData[i]);
                if (i < inputData.size() - 1)
                    secondInput.push_back(inputData[1 + i]);
            }
            auto output1 = singleOutlierRemove(firstInput);
            auto output2 = singleOutlierRemove(secondInput);

            for (size_t i = 0; i < output1.size(); i++)
            {
                if (i * 2 < outputData.size())
                    outputData[i * 2] = output1[i];
            }

            for (size_t i = 0; i < output2.size(); i++)
            {
                if (i * 2 + 1 < outputData.size())
                    outputData[1 + i * 2] = output2[i];
            }
            return outputData;
        }

        vectord voltageCal(const vectord &inputData, double maxVolt, double minVolt, int maxVal, int minVal)
        {
            auto ratio = (maxVolt - minVolt) / (maxVal - minVal);
            vectord output;
            for (auto &e : inputData)
            {
                auto voltage = (e - minVal) * ratio + minVolt;
                output.push_back(voltage);
            }
            return output;
        }

        std::vector<std::vector<double>> splitByIndex(const vectord &inputData, int chunkSize, int stepSize)
        {
            std::vector<std::vector<double>> output;
            int currentStartIndex = 0;
            int currentEndIndex = 0;
            while (currentStartIndex + chunkSize < inputData.size())
            {
                currentEndIndex = currentStartIndex + chunkSize;
                auto temp = mathtool::truncate(inputData, currentStartIndex, currentEndIndex);
                output.push_back(temp);
                currentStartIndex += stepSize;
            }
            return output;
            
        }

        std::vector<std::vector<double>> splitByTime(const vectord &inputData, float chunkSec, float stepSec, float fs)
        {
            int chunkSize = static_cast<int>(chunkSec*fs);
            int offsetSize = static_cast<int>(stepSec*fs);
            return splitByIndex(inputData, chunkSize, offsetSize);
        }
    } // namespace dsp

} // namespace basic