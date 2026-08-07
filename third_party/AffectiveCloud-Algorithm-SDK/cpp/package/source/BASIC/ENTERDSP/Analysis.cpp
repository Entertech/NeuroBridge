#include "Analysis.h"
#include "VectorExtention.h"
namespace basic
{

    namespace dsp
    {
        SingleEEGData singleEegDa(const std::vector<uint8_t>& rawData, int pckNum, int pckByte, int headByte, int dataByte)
        {
            SingleEEGData seegData;
            std::vector<int> eegData;
            if (rawData.size() == pckNum * pckByte) {
                for (int i = 0; i < pckNum; ++i) {
                    auto pckRawData = std::vector<uint8_t>(rawData.begin() + i * pckByte, rawData.begin() + (i + 1) * pckByte);
                    for (int j = 0; j < 5; ++j) {
                        eegData.push_back(
                                static_cast<int>(
                                        static_cast<uint32_t>(pckRawData[headByte + j * dataByte]) << 16 |
                                        static_cast<uint32_t>(pckRawData[headByte + j * dataByte + 1]) << 8 |
                                        static_cast<uint32_t>(pckRawData[headByte + j * dataByte + 2])
                                )
                        );
                    }
                }
            }
            seegData.eeg = eegData;
            return seegData;
        }

        DoubleEEGData doubleEEGDa(std::vector<uint8_t> rawData, int pckNum, int pckByte, int headByte, int dataByte)
        {

            DoubleEEGData eegData;
            if (static_cast<int>(rawData.size()) == pckNum * pckByte)
            {
                for (int i = 0; i < pckNum; i++)
                {
                    auto pckRawData = tools::cutArrs(rawData, i * pckByte, (i + 1) * pckByte);

                    for (size_t j = 0; j < 3; j++)
                    {
                        auto tempLeftBytes = tools::cutArrs(pckRawData, j * 2 * dataByte + headByte, (j * 2 + 1) * dataByte + headByte);

                        int tempLeft = (tempLeftBytes.at(0) << 16) | (tempLeftBytes.at(1) << 8) | tempLeftBytes.at(2);
                        eegData.left.push_back(tempLeft);

                        auto tempRightBytes = tools::cutArrs(pckRawData, (j * 2 + 1) * dataByte + headByte, (j + 1) * 2 * dataByte + headByte);
                        int tempRight = (tempRightBytes.at(0) << 16) | (tempRightBytes.at(1) << 8) | tempRightBytes.at(2);
                        eegData.right.push_back(tempRight);
                    }
                }
            }
            return eegData;
        }

        HRData hrDa(std::vector<uint8_t> rawData, int pckNum, int pckByte, int headByte, int dataByte)
        {
            headByte++;
            dataByte++;
            HRData hrData;
            if (static_cast<int>(rawData.size()) == pckNum * pckByte)
            {
                for (int i = 0; i < pckNum; i++)
                {
                    int tempHr = rawData.at(i);
                    hrData.hr.push_back(tempHr);
                }
            }
            return hrData;
        }

        PEPRData singlePeprDa(std::vector<uint8_t> rawData, int pckNum, int pckByte, int headByte, int dataByte)
        {
            PEPRData peprData;
            if (static_cast<int>(rawData.size()) == pckNum * pckByte)
            {
                for (int i = 0; i < pckNum; i++)
                {
                    auto pckRawData = tools::cutArrs(rawData, i * pckByte, (i + 1) * pckByte);
                    for (size_t j = 0; j < 5; j++)
                    {
                        auto tempPeBytes = tools::cutArrs(pckRawData, j * dataByte + headByte, (j + 1) * dataByte + headByte);
                        int tempPe = (tempPeBytes.at(0) << 8) | tempPeBytes.at(1);
                        peprData.pe.push_back(tempPe);
                    }
                    auto tempPrBytes = tools::cutArrs(pckRawData, 5 * dataByte + headByte, 6 * dataByte + headByte);
                    int tempPr = (tempPrBytes.at(0) << 8) | tempPrBytes.at(1);
                    peprData.pr.push_back(tempPr);
                }
            }
            return peprData;
        }



    } // namespace dsp

} // namespace basic
