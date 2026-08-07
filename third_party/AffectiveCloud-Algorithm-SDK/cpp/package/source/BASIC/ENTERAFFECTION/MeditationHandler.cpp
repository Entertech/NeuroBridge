#include "MeditationHandler.h"
#include "ModelAlgorithm.h"
#include "MathTool.h"
#include "AiNormParams.h"
#include "AiMeditationModel.hpp"
#include "Basic.hpp"

namespace basic
{
    namespace affection
    {
        namespace handler
        {
            /// @brief 冥想处理器
            /// @param eegFeature 脑电特征
            /// @param eegQuality 脑电信号质量
            /// @param tmp 累计运行时长
            /// @param smoothScope 冥想度平滑范围
            /// @return
            MeditationHandlerResult aiMeditationHandler(nc::NdArray<double> eegFeature,
                                                      int eegQuality, double timeCum, AiMeditationHandlerTemp &tmp, int smoothScope)
            {
                // 特征合并
                auto eegFeatureCom = nc::reshape(eegFeature, nc::Shape(1, -1));

                // 归一化
                auto meditationSVMNorm = params::meditationSVMNormParam.transpose();
                auto eegFeatureNorm = mathtool::featureNorm(eegFeatureCom, meditationSVMNorm, "sym");

                // 网络预测
                auto modelRes = meditationModel::predict(&eegFeatureNorm.row(0).toStlVector()[0]);

                // 冥想状态计算
                define::MeditationState curMeditationState = define::MeditationState::ACTIVE;
                if (modelRes < 0.5)
                    curMeditationState = define::MeditationState::FLOW;

                for (size_t i = 0; i < tmp.meditationStateStore.size() - 1; i++)
                {
                    tmp.meditationStateStore[i] = tmp.meditationStateStore[i + 1];
                }
                tmp.meditationStateStore[tmp.meditationStateStore.size() - 1] = curMeditationState;

                define::MeditationState meditationState = define::MeditationState::ACTIVE;
                if (timeCum >= 30.0)
                {
                    size_t activeCount = 0;
                    size_t flowCount = 0;
                    for (auto &e : tmp.meditationStateStore)
                    {
                        if (e == define::MeditationState::ACTIVE)
                            activeCount++;
                        else
                            flowCount++;
                    }
                    if (activeCount >= 3)
                        meditationState = define::MeditationState::ACTIVE;
                    else if (flowCount >= 3)
                        meditationState = define::MeditationState::FLOW;
                }
                tmp.meditationState = meditationState;

                // 冥想度计算
                auto curMeditationDegree = model::meditationDegreeCal(1-modelRes);
                MeditationHandlerResult result;
                result.meditationState = meditationState;
                if (curMeditationDegree >= 0)
                {
                    if (tmp.meditationDegreeStore.size() >= smoothScope)
                    {
                        std::copy(tmp.meditationDegreeStore.begin()+1, tmp.meditationDegreeStore.end(), tmp.meditationDegreeStore.begin());
                        tmp.meditationDegreeStore[smoothScope-1] = curMeditationDegree;
                    } else {
                        tmp.meditationDegreeStore.push_back(curMeditationDegree);
                    }
                    auto degreeMean = mathtool::mean(tmp.meditationDegreeStore);
                    if (degreeMean > 100) 
                        degreeMean = 99.99;
                    else if (degreeMean < 0)
                        degreeMean = 0;
                    result.meditationDegree = degreeMean;
                    
                }

                return result;
            }
        }
    }
}