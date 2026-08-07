#ifndef CE63381A_72D8_4E6B_9901_D3C156636A11
#define CE63381A_72D8_4E6B_9901_D3C156636A11
#include "NumCpp.hpp"
#include <vector>
#include "TypeDefine.h"
#include "AffectionData.h"
namespace basic::affection::model
{
    nc::NdArray<double> rulerAdjust(const nc::NdArray<double> &ruler, double offset, double offsetRatio = 1);

    void rulersCalibration(std::vector<nc::NdArray<double>> &featuresStore, const vectord &newFeatures,
                           std::vector<nc::NdArray<double>> &curRulers,
                           const std::vector<nc::NdArray<double>> &oriRulers, int adjScope,
                           int adjPeriod, double offsetRatio);

    double rulerMap(double value, const nc::NdArray<double> &ruler, double stretchRatio = 0, int mapMin = 0, int mapMax = 100);

    double outputProcess(double curValue, nc::NdArray<double> &valueStore, double weight, double initialVal, int initialScope);

    double attentionCal(vectord features, const std::vector<nc::NdArray<double>> &rulers);

    double relaxationCal(vectord features, const std::vector<nc::NdArray<double>> &rulers, bool singleChn);

    /// @brief 冥想度计算
    /// @param features 特征
    /// @param rulers 衡量冥想度值的标尺
    /// @param ratios 标尺缩放比例
    /// @param weights 特征权重
    /// @param bias 特征偏置
    /// @return 冥想度值
    double meditationCal(vectord features, const std::vector<nc::NdArray<double>> &rulers, vectord ratios, vectord weights, vectord bias);

    double pressureCal(double hrVal, double hrvVal, double hrLf, double freqRate);

    double pleasureCal(const vectord &features, const std::vector<nc::NdArray<double>> &rulers);

    double arousalCal(double hrVal, double freqRate);

    double coherenceCal(double syncFreqCor);

    define::SleepPhaseEnum sleepPhaseCal(vectord modelRes);

    double sleepDegreeCal(double beta, double theta, double delta, double gamma, vectord modelRes, bool sleepFlag);

    /// 睡眠分期计算
    /// \param modelRes 模型输出
    /// \return 睡眠相位
    define::SleepStage sleepStageCal(vectord modelRes);

    /// @brief 冥想程度计算
    /// @param modelRes 模型输出
    /// @return 冥想度
    double meditationDegreeCal(double res);


} // namespace basic


#endif /* CE63381A_72D8_4E6B_9901_D3C156636A11 */
